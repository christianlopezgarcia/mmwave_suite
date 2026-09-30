/*
* Copyright (C) 2024 Texas Instruments Incorporated
*
* All rights reserved not granted herein.
* Limited License.  
*
* Texas Instruments Incorporated grants a world-wide, royalty-free, 
* non-exclusive license under copyrights and patents it now or hereafter 
* owns or controls to make, have made, use, import, offer to sell and sell ("Utilize")
* this software subject to the terms herein.  With respect to the foregoing patent 
* license, such license is granted  solely to the extent that any such patent is necessary 
* to Utilize the software alone.  The patent license shall not apply to any combinations which 
* include this software, other than combinations with devices manufactured by or for TI ("TI Devices").  
* No hardware patent is licensed hereunder.
*
* Redistributions must preserve existing copyright notices and reproduce this license (including the 
* above copyright notice and the disclaimer and (if applicable) source code license limitations below) 
* in the documentation and/or other materials provided with the distribution
*
* Redistribution and use in binary form, without modification, are permitted provided that the following
* conditions are met:
*
*	* No reverse engineering, decompilation, or disassembly of this software is permitted with respect to any 
*     software provided in binary form.
*	* any redistribution and use are licensed by TI for use only with TI Devices.
*	* Nothing shall obligate TI to provide you with source code for the software licensed and provided to you in object code.
*
* If software source code is provided to you, modification and redistribution of the source code are permitted 
* provided that the following conditions are met:
*
*   * any redistribution and use of the source code, including any resulting derivative works, are licensed by 
*     TI for use only with TI Devices.
*   * any redistribution and use of any object code compiled from the source code and any resulting derivative 
*     works, are licensed by TI for use only with TI Devices.
*
* Neither the name of Texas Instruments Incorporated nor the names of its suppliers may be used to endorse or 
* promote products derived from this software without specific prior written permission.
*
* DISCLAIMER.
*
* THIS SOFTWARE IS PROVIDED BY TI AND TI'S LICENSORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, 
* BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. 
* IN NO EVENT SHALL TI AND TI'S LICENSORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR 
* CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, 
* OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, 
* OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE 
* POSSIBILITY OF SUCH DAMAGE.
*/

/*
 * gesture.h
 *
 */

#ifndef GESTURE_H_
#define GESTURE_H_

/* MMWAVE library Include Files */
#include <ti/common/sys_common.h>
#include <ti/drivers/uart/UART.h>
#include <ti/utils/cli/cli.h>

#ifdef __cplusplus
extern "C"
{
#endif

#define NUM_GESTURE_FEATURES        6 // Number of features used for ann inference
#define NUM_GESTURE_FEATURES_OUTPUT 10 // Number of features to output
#define GESTURE_FEATURE_LENGTH      15 // Number of frames to include as input to ann
#define FEATURE_VECTOR_SIZE         GESTURE_FEATURE_LENGTH *NUM_GESTURE_FEATURES

/* The below defines are describe the structure of the ann */
#define INP_DIM                  FEATURE_VECTOR_SIZE // Size of ann input
#define NUM_NODES_FIRST_LAYER    30 // Number of nodes in the first hidden layer
#define NUM_NODES_SECOND_LAYER   60 // Number of nodes in the second hidden layer
#define NUM_NODES_THIRD_LAYER    10 // Number of nodes in the third layer
#define NUM_OUTPUT_PROBABILITIES 10 // Number of ann output probabilities

/* The below defines are for feature generation */
#define NUM_ANGLE_BINS_FEATURES  32
#define THRESH_NUM_POINTS        2000
#define NUM_SORTED_VALUES        10
#define LEN_CORR                 20
#define DOPPLER_BINS_TO_SUPPRESS 5
#define PHASE_CORRECTION         -1
#define RANGE_BIN_START          1
#define RANGE_BIN_END            8

/* The below defines are the index values of ann output probabilities for each gesture */
#define IDX_NO_GESTURE        0
#define IDX_L2R_GESTURE       1 // Swipe:Left-2-Right
#define IDX_R2L_GESTURE       2 // Swipe:Right-2-Left
#define IDX_U2D_GESTURE       3 // Swipe:Up-2-Down
#define IDX_D2U_GESTURE       4 // Swipe:Down-2-Up
#define IDX_CW_TWIRL_GESTURE  5 // Volume Up:Clockwise rotation with finger
#define IDX_CCW_TWIRL_GESTURE 6 // Volume Down: Anti-Clockwise rotation with finger
#define IDX_OFF_GESTURE       7 // Off Gesture: Movement of hand towards radar
#define IDX_ON_GESTURE        8 // On Gesture: Movement of hand away from radar
#define IDX_SHINE_GESTURE     9 // Shine Gesture

    /* The below values are the probability thresholds for a gesture to be detected */
    static float GESTURE_PROBABILITY_THRESHOLDS[NUM_OUTPUT_PROBABILITIES] = {
        0.6, // Threshold for No Gesture
        0.6, // Threshold for L2R
        0.6, // Threshold for R2L
        0.6, // Threshold for U2D
        0.6, // Threshold for D2U
        0.9, // Threshold for CW_TWIRL
        0.9, // Threshold for CCW_TWIRL
        0.6, // Threshold for OFF
        0.6, // Threshold for ON
        0.99 // Threshold for SHINE
    };

    /* The below values are the count thresholds for a gesture to be detected */
    static uint8_t GESTURE_COUNT_THRESHOLDS[NUM_OUTPUT_PROBABILITIES] = {
        4, // Threshold for No Gesture
        4, // Threshold for L2R
        4, // Threshold for R2L
        4, // Threshold for U2D
        4, // Threshold for D2U
        9, // Threshold for CW_TWIRL
        9, // Threshold for CCW_TWIRL
        4, // Threshold for OFF
        4, // Threshold for ON
        8 // Threshold for SHINE
    };

    static char *GEST_OUTPUTS[] = {
        "NO GESTURE DETECTED \n",
        "LEFT-TO-RIGHT \n",
        "RIGHT-TO-LEFT \n",
        "UP-TO-DOWN \n",
        "DOWN-TO-UP \n",
        "TWIRL-CW \n",
        "TWIRL-CCW \n",
        "OFF-GESTURE \n",
        "ON-GESTURE \n",
        "SHINE-GESTURE \n"
    };

    typedef struct Features_t_
    {
        float pWtdoppler;
        float pWtdopplerPos;
        float pWtdopplerNeg;
        float pWtrange;
        float pNumDetections;
        float pWtaz_mean;
        float pWtel_mean;
        float pAzdoppcorr;
        float pWtaz_std;
        float pWtel_std;

        float pInstenergy;

        float   dopp_corr_buf[LEN_CORR];
        int32_t az_corr_buf[LEN_CORR];

        uint16_t numRangeBins;
        uint16_t numDopplerBins;
        uint8_t  numVirtualAntennas;

        cmplx32ImRe_t *dopplerCubeData;
        cmplx32ImRe_t *radarCubeData;
        uint32_t      *detMatrixData;
        uint32_t      *angleMatrixData;

        uint8_t rangeIndex;
        uint8_t dopplerIndex;
        int8_t  azimuthIndex;
        int8_t  elevationIndex;

        float currentWeight;

        uint32_t  frameCount;
        uint32_t *max_value_ptr;

        uint8_t sumProbs[GESTURE_FEATURE_LENGTH * NUM_OUTPUT_PROBABILITIES];
        uint8_t prevFramegesture;
        uint8_t currFramegesture;

        uint8_t countDOA;

        uint32_t HWA_base_address;
    } Features_t;

    /**
     * @brief
     *  Millimeter Wave Demo Data Path ANN based detection weights, biases, internal/final outputs
     *
     * @details
     *  The structure is used to hold all the relevant information for ANN based detection in
     *  the data path.
     */
    typedef struct ANN_struct_t_
    {
        float mean[NUM_GESTURE_FEATURES];
        float std[NUM_GESTURE_FEATURES];
        float b_0[NUM_NODES_FIRST_LAYER];
        float b_1[NUM_NODES_SECOND_LAYER];
        float b_2[NUM_NODES_THIRD_LAYER];
        float W_0[NUM_NODES_FIRST_LAYER][INP_DIM];
        float W_1[NUM_NODES_SECOND_LAYER][NUM_NODES_FIRST_LAYER];
        float W_2[NUM_NODES_THIRD_LAYER][NUM_NODES_SECOND_LAYER];
        float op_layer1[NUM_NODES_FIRST_LAYER];
        float op_layer2[NUM_NODES_SECOND_LAYER];
        float op_layer3[NUM_NODES_THIRD_LAYER];
        float prob[NUM_NODES_THIRD_LAYER];
    } ANN_struct_t;

    void Computefeatures_preStart(void);
    void Computefeatures_RDIBased(void);
    void Computefeatures_DOABased(void);
    void ComputeAngleStats(void);
    void Computefeatures_Hybrid();
    void FindGesture();

#endif /* GESTURE_H_ */
